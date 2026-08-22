# x-preview-f-free (Ox Alpha) 主力化稳定方案（2026-08-21）

用户决策：x-preview-f-free 提升为主力模型，必须稳定；池内均为免费渠道；
不做渠道亲和（2026-08-09 已论证亲和在故障渠道上钉死请求，零收益）。

## 池结构（优先级高→低）

| ch | 名称 | p | 上游本质 | 额度 |
|----|------|---|---------|------|
| 96 | opencode-zen-free | 10 | OpenCode Zen 免费档 | 突发 ~15-20 RPM（社区实测，官方未公布） |
| 102 | ai-168661-ox-alpha | 7 | OpenRouter 中转 | 共享 OR 账号级日额度 1000/天 |
| 103 | s608885-ox-alpha | 6 | OpenRouter 中转 | 同上（同一 cap） |
| 100 | openrouter-ox-alpha | 5 | OpenRouter 直连 | 同上（同一 cap） |
| 104 | opencode-go-oxalpha | 4 | OpenCode Go 套餐 | $60/月美元额度，垫底兜底 |

关键事实（2026-08-21 实测 + 社区调研）：

- ch102/103/100 的 429 都是 `limit_source=openrouter_free_tier_daily`——三个
  渠道共享同一个 OpenRouter 账号级日额度，实际独立上游只有 Zen + OR 两家。
  OR 日额度每天 08:00 (+08) 重置（X-RateLimit-Reset 实测值）。
- OpenRouter 官方：失败请求也计入日额度。额度耗尽后继续白试会延长死亡窗口，
  所以 Guardian 现在对日额度类 429 直接禁用挂墓碑（见下）。
- Zen 免费档间歇 503/EOF/524（Cloudflare 120s 读超时），社区 5 小时观测
  不可用率 ~14%。属预期抖动，靠 NewAPI 池内重试 + OMP fallback 吸收。
- Go 档 `ox-alpha-free` 与免费档是独立配额链路（订阅美元额度），作为 p4
  垫底：免费层全灭时池子不死，平时不烧 Go 额度。

## Guardian 日额度墓碑（2026-08-21 落地）

`guardian.py` 新增 `_daily_cap_reset_iso()`：命中
`free-models-per-day` / `openrouter_free_tier_daily` /
`daily_free_credits_exhausted` 时，error scan 和 full scan 不再按瞬态
限流跳过，而是禁用渠道并在 state 记录 `daily_cap_until`（优先解析
X-RateLimit-Reset，毫秒/秒自适应、大小写不敏感；缺失或异常时保守 3h；
超出 36h 的 reset 视为不可信同样回落 3h；reset 刚过去 30 分钟内钳位为
立即恢复探测）。恢复侧在重置点之前跳过墓碑记录（不烧恢复配额），到点后
走正常 3 探针恢复流程，成功自动回池。

同 id 已有旧记录时原地刷新 `daily_cap_until`/reason/time，不走
`_append_disabled` 的按 id 去重（否则告警声称的恢复时间与 state 实际
记录不一致——2026-08-21 深夜 review 修复，含回归测试）。回落时长从
12h 收紧到 3h 的同轮修复原因：ch93（sotamodel）报文不带 reset 头，
12h 会对 ~08:00 的重置点过冲数小时。

注意：`daily_free_credits_exhausted` 同时覆盖 ch93（sotamodel sota 线），
墓碑机制让 ch93 的夜间停机恢复点也更精确（此前靠全量扫描碰运气）。

## ch102 key 轮换（2026-08-22）

ai.168661.xyz 重发了 ox-alpha 家族 key（该站契约：每个模型家族一个
key）。老 key 上游返回 401 Invalid token，已死。按站点的单家族单 key
契约做了**轮换**而非加第二渠道——`scripts/ops/rotate_ai168661_ox_alpha_key.py`
备份整库后 PUT 更新 ch102，仅 key 变化，name/models/mapping/p7/w5/
status/header_override 回读逐项验证不变，management probe
（x-preview-f-free→ox-alpha）转绿。

经验：NewAPI `PUT /api/channel/` 的 body 是 channel 结构体**本体**
（create 才是 `{"mode","channel"}` 包装，错用包装报 "record not
found"）；且必须带列表 API 返回的**完整投影**（去掉 status），手挑子集
报 "Invalid parameters"。脚本里已注释。

## ch15 reasoning_effort 条件钳制（2026-08-22）

日志复查发现：muse-free/x-preview-f-free 的 efforts 白名单含 `max`，OMP
故障转移到 `deepseek-v4-flash`（池主力 ch15）时**沿用原 effort**，ch15
上游不收 `max` → 400 `field ReasoningEffort invalid, should be one of:
low, medium, high, xhigh, none`（24h 内 6 次）。OMP 发的是顶层
`reasoning_effort`（snake_case，已用 http-400 落盘请求证实）。

修复：ch15 加条件 param_override（上游 new-api `relay/common/override.go`
的 operations DSL——conditions 支持 full/prefix/contains/gt 等 + invert，
操作为 set）：

```json
{"operations": [{"mode": "set", "path": "reasoning_effort", "value": "xhigh",
  "conditions": [{"mode": "full", "path": "reasoning_effort", "value": "max"}],
  "logic": "AND"}]}
```

只在 effort 恰好为 `max` 时钳到 `xhigh`（上游白名单最高档），其余档位
透传，主路 max 不受影响。脚本
`scripts/ops/clamp_ch15_reasoning_effort.py`：复现 400 → 备份 → 打
override → max/xhigh/无 effort 三组探针全 200 → 回读验证。重跑幂等
（已存在则只验证）。

注意范围：只修 ch15。其他渠道若也不认 `max`，同法加钳制即可；OMP 侧
fallback 不重夹 effort 是结构性行为，目前靠各渠道钳制兜底。

## 运维要点

- OR 日额度耗尽是日常事件（主力化后 1000/天大概率不够用）。墓碑禁用后
  池内剩余 Zen + Go 两路，advisor/default 兜底链不受影响。
- 若 OR 账号曾充值 ≥$10 可升 1000/天上限；未充值账号只有 50/天
  （官方文档）。多 key 无效——限制是账号级的。
- Zen 突发限流无 Retry-After 头，只能按瞬态 429 跳过，不做墓碑。
- 相关脚本：`scripts/ops/add_opencode_go_oxalpha_channel.py`（ch104 重建/
  验证）、`add_ai168661_ox_alpha_channel.py`、`add_608885_ox_alpha_channel.py`、
  `add_openrouter_ox_alpha_channel.py`、`add_opencode_zen_free_channels.py`。

## 参考

- OpenRouter 限流规则: https://openrouter.ai/docs/api_reference/limits
- Zen 稳定性社区报告: anomalyco/opencode#36889 / #40886 / #42279
- NewAPI 半开恢复特性请求（未实现，故用 Guardian 墓碑）:
  QuantumNous/new-api#5420
