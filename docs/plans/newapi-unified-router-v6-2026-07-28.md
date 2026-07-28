# NewAPI 路由架构 v6：从缝缝补补到统一治理

## 一、诊断：为什么一直在缝补

### 根因：三套调权系统各自为政 + NewAPI retry 竞态

| 系统 | 周期 | 改什么 | 状态文件 | 盲区 |
|------|------|--------|----------|------|
| `route_optimizer.py` v5 | */5 cron | channels.weight (Opus+Sonnet) | `route_optimizer_state.json` | 只探 5 个渠道，#11 曾被 EXCLUDE 导致挂了不降权 |
| `autoweight.py` | */2h cron | channels.weight (guard 渠道) | `autoweight-cooldown.json` | 3h 冷却太长；和 optimizer 改同一批 weight 互相覆盖 |
| `analyze_newapi_dx.py` | 手动/DX loop | channels.weight + guard env | `dx_ops_state.json` | 不在 cron 里，和 optimizer 竞争写 weight |
| `health_check.py` v5.1 | */30 cron | channels.status (disable only) | `health_state.json` | 只探 4 个渠道；disable 后无自动复活 |

**四个脚本四种状态文件，互不感知，改同一张 channels 表的 weight/status。** route_optimizer 5 分钟前给 #20 设 w24，autoweight 2 小时后可能覆盖成别的值，dx 手动跑一次又覆盖一次。

### NewAPI 原生 retry 有已知竞态（#6095）

社区 issue [#6095](https://github.com/QuantumNous/new-api/issues/6095) 确认：retry index 直接当数组下标，自动禁用 goroutine 异步删渠道导致下标错位 → retry 跳过本应选中的渠道。**我们的 RetryTimes=4 叠加在上面，每次重试都是赌运气。**

### 流式中途失败无法 failover（行业共识）

社区共识（claude-code-hub #916、quant67 网关篇）：一旦开始返回 token，就无法切渠道。kiro_guard tee 模式已经用「末尾校验 + controller.error()」缓解了截断感知，但 **tee 的软重试只能在同一上游重打**，跨渠道切换仍然只靠 NewAPI 的首-token-fail retry。

### 具体"慢"的实测证据（2026-07-28 12:xx）

- #20 baibei `claude-opus-5` 单请求 **89 秒**；#9 opus-4-8 **56 秒**
- #11 林夕 API 30 秒超时（首页 0.5s 活着但推理挂）——因 `EXCLUDE_CHANNELS={11}` 不被探针监控
- 50 层各渠道最近 1h 平均 use_time：#9 20.8s / #20 17.8s / #60 21.6s / #11 间歇超时

---

## 二、设计目标

1. **单一调权源**：一个脚本管所有渠道权重，消除三方竞争
2. **真实流量驱动**：logs 表 use_time + 探针 TTFT 双信号，不靠单一探针
3. **P2C + EWMA 打分**：业界验证的尾延迟感知路由（比当前 score=1/lat 强）
4. **断路器三态**：CLOSED → OPEN(熔断) → HALF_OPEN(探针) → CLOSED，替代当前"降权但不摘渠"的模糊状态
5. **可观测**：一个 dashboard 看到所有渠道的延迟/错误率/权重/状态趋势
6. **不动 NewAPI fork**：仍然是外挂脚本，不引入新依赖

---

## 三、架构设计

### 3.1 统一路由控制器（`unified_router.py`）

**替代** route_optimizer.py + autoweight.py + health_check.py 的调权部分。一个脚本，一份状态。

```
/opt/new-api/scripts/unified_router.py     ← 新（取代三旧）
/opt/new-api/data/router_state.json        ← 新（统一状态）
/opt/new-api/data/router_config.yaml       ← 新（可配，取代硬编码常量）
```

### 3.2 核心评分：P2C + Peak-EWMA

来源：[quant67 P2C/EWMA 深度解析](https://quant67.com/post/algorithms/69-load-balancing/load-balancing.html)、[Finagle p2cPeakEwma](https://github.com/twitter/finagle)、Envoy LEAST_REQUEST。

```python
# 每个渠道维护：
#   ewma_ttft  — 首字节延迟 EWMA（半衰期 = 该层 P99 × 20，动态）
#   ewma_err   — 错误率 EWMA（分级：auth×3, 5xx×2, 429×0.5, content×0）
#   inflight   — 当前在途请求数（从 NewAPI logs 估算 / 或 guard 共享）
#   weight     — 写回 DB 的权重

score = ewma_ttft * (1 + ewma_err * ERR_PENALTY) * (1 + inflight * INFLIGHT_WEIGHT) / base_weight
# score 越低越好（延迟低、错误少、空闲）
# 归一化为 weight：weight_i = round(100 * (1/score_i) / Σ(1/score_j))，clamp [W_MIN, W_MAX]
```

**与当前 route_optimizer 的关键差异**：
- 当前用 `score = quality / max(lat, 0.3)` → 延迟信号单一（探针 ttft），不感知并发
- 新方案引入 `inflight`（在途请求数）—— 当 #20 有 3 个在途请求时，即使它 TTFT 低也会被适当降权，避免排队
- Peak-EWMA 保留峰值记忆（JVM GC 式停顿、上游限流尖峰），比普通 EWMA 对间歇性问题更敏感

### 3.3 断路器三态机（替代"降权但不摘渠"）

来源：[quant67 熔断](https://quant67.com/post/algorithms/69-load-balancing/load-balancing.html) §三态机、Envoy Outlier Detection。

```
状态机：
  CLOSED（正常）──错误率 > 50%（10s 窗口, ≥5 请求）──> OPEN（熔断, weight=0）
  OPEN ──30s 后──> HALF_OPEN（放 1 个探针请求）
  HALF_OPEN ──探针成功──> CLOSED（weight 恢复）
  HALF_OPEN ──探针失败──> OPEN（重置 30s 计时器）

panic threshold: 当一层内 OPEN 渠道 > 70% 时，所有 OPEN 渠道降级为 HALF_OPEN
                 （避免全标不健康导致无渠道可用，借鉴 Envoy panic threshold）
```

**与当前的差异**：
- 当前 health_check 只 disable 不 enable（无自动复活），route_optimizer 降权到 w1 但渠道仍接流量
- 断路器 OPEN = weight=0 = 不接流量；HALF_OPEN = 只接探针；CLOSED = 正常。状态明确

### 3.4 渠道分层统一管理

```yaml
# router_config.yaml
tiers:
  opus_main:          # priority 50
    channels: [9, 10, 11, 20, 60, 127]
    probe_model: "claude-opus-5"
    probe_interval: 300        # 5 min
    w_min: 3                   # ε-greedy 下限
    w_max: 60
    breaker:
      error_rate_threshold: 0.5
      min_requests: 5
      window_seconds: 30
      cooldown_seconds: 30
    score_inflight_weight: 0.3
    
  opus_fallback:      # priority 45 (GPT/k3/grok 映射渠)
    channels: [140, 13, 131, 96, 139, 142]
    margin_gate: 1.3           # 必须比 opus_main 最优渠道好 1.3x 才给权重
    mapped_cap: 25             # 映射渠权重上限
    
  sonnet_chain:       # priority < 0
    channels: [63, 133, 129, 134, 136]
    swap_margin: 1.5           # 下层好 1.5x 才换序
```

### 3.5 信号采集统一

| 信号 | 来源 | 频率 |
|------|------|------|
| TTFT 探针 | 真实 token 请求（max_tokens=8, stream=true, 量首字节） | 每 5 min/渠道 |
| 真实流量延迟 | `logs` 表 `use_time` 字段（EWMA α=0.4） | 实时（logs 自动入库） |
| 错误率 | podman logs 解析 `channel error (channel #N` + 分级 | 每 5 min 扫 6min 窗口 |
| guard 硬失败 | `kiro-guard-metrics-{port}.json` 的 `hard` 计数 | 每 5 min 读快照 |
| 在途请求 | NewAPI 无原生 API → 用「最近 30s logs 计数 × 平均 use_time」估算 | 每 30s |

### 3.6 流式截断的独立解法（kiro_guard 已在线，不重叠）

kiro_guard tee 模式已在生产处理截断（short_completion 续写、missing_stop_reason 检测、403→503 failover）。**unified_router 不碰流式逻辑**，只管 weight/status。guard 的 hard fail 计数作为信号输入断路器。

---

## 四、实施计划（四阶段）

### Phase 1：统一调权源（核心，1-2 天）

**目标**：消灭三套脚本竞争，一个脚本管全部。

1. 写 `unified_router.py`：
   - 从 route_optimizer.py v5 继承 TTFT 探针 + 错误分级 + logs 统计
   - 从 autoweight.py 继承 guard metrics 读取
   - 新增 P2C+EWMA 评分 + inflight 估算
   - 新增断路器三态机
   - 新增 router_config.yaml（渠道列表、阈值全可配，不硬编码）
2. **干跑模式**（`--dry-run`）：只输出建议权重 + 断路器状态，不写 DB。跑 24h 对比和旧脚本的决策差异
3. 切换：停旧 cron（route_optimizer + autoweight + health_check 的调权部分），启新 cron
4. 旧脚本保留 `.bak` 不删，回退 = 恢复旧 cron

**验收**：
- `--dry-run` 输出和 route_optimizer 在相同输入下 weight 差异 < ±10（对齐验证）
- 手动 kill 一个渠道的 guard 进程，断路器 30s 内 OPEN → weight=0 → 流量停止打到它
- 恢复后 30s 内 HALF_OPEN → 探针成功 → CLOSED → weight 恢复
- 连续 24h 无 weight 抖动（同渠道 weight 变化频率 < 旧方案 50%）

### Phase 2：真实流量信号 + 缓存命中追踪（1 天）

**目标**：让打分基于真实用户体验，不只是探针。

1. 从 `logs` 表每轮采集真实 `use_time` 分布（p50/p90/p99），按渠道聚合
2. 追踪 `cache_tokens` / `cache_creation_tokens` 比率——高缓存命中的渠道加权（对用户更便宜更快）
3. inflight 估算：用「最近 60s 该渠道成功请求数 × 平均 use_time / 60」估算并发占用

**验收**：
- logs 表有数据的渠道，评分权重中真实流量信号占比 ≥ 60%（探针 ≤ 40%）
- 缓存命中率 > 80% 的渠道获得 visible 加权（至少 +20% weight）

### Phase 3：可观测性 dashboard（1 天）

**目标**：一个页面看到所有渠道状态，不用 SSH 翻日志。

1. `unified_router.py --report` 生成 markdown 日报（每 5 min 覆写到 `/opt/new-api/reports/router-live.md`）
2. 内容：每渠道的 EWMA_TTFT / 错误率 / 断路器状态 / 当前权重 / 24h 趋势
3. TG bot 增加命令 `/router` 实时返回当前路由表快照

### Phase 4：NewAPI retry 竞态缓解 + affinity 策略优化（1 天）

**目标**：在不动 fork 的前提下，减少 retry 跳过好渠道的概率。

1. **降低 RetryTimes**：4 → 2（减少竞态暴露面；断路器 + guard 已提供更智能的 failover）
2. **affinity ttl 分层优化**：claude 规则当前 ttl=15s（极短，几乎每次请求都重新选渠道）。评估提到 60-120s，在缓存有效期内钉死同一渠道
3. **skip_retry_on_failure 保持 false**（当前已修），配合断路器 OPEN 状态的 weight=0，让 NewAPI 自然跳过死渠道

**验收**：
- retry 竞态导致的"跳过好渠道"现象在日志中消失（检查 `use_channel` 链是否有跳跃）
- affinity ttl 提升后 cache_tokens 比率提升 ≥ 15%

---

## 五、不做什么（防止过度工程）

- ❌ **不替换 NewAPI**（LiteLLM/gpt-load 替换成本高，丢计费/分组/渠道管理）
- ❌ **不改 NewAPI fork Go 代码**（升级维护负担大，外挂脚本够用）
- ❌ **不做 hedged requests**（kiro_guard 已立项但未做——并发请求翻倍费用，ROI 不够，等基础设施稳定后再评估）
- ❌ **不做客户端层 auto-continue**（浏览器 DOM 方案不解决网络截断，已有 kiro_guard tee）
- ❌ **不动 cc-switch**（AGENTS.md 约束：只改 NewAPI + provider env）

---

## 六、风险与回退

| 风险 | 概率 | 回退 |
|------|------|------|
| unified_router 打分 bug 导致权重全偏 | 中 | `--dry-run` 24h 验证；旧脚本 + cron 保留，恢复 = 取消注释旧 cron |
| 断路器过于激进，频繁 OPEN 导致可用渠道减少 | 中 | panic threshold 保底（70% OPEN 时全降 HALF_OPEN）；error_rate_threshold 可配，先宽松后收紧 |
| inflight 估算不准（NewAPI 无原生在途 API） | 高 | Phase 2 先验证估算准确度，不准则降权到 0.1 或移除，回退纯 TTFT 评分 |
| NewAPI retry 竞态在 RetryTimes=2 时仍出 | 低 | #6095 是上游 bug，我们只能减暴露面；最坏情况是退回 RetryTimes=4 |

---

## 七、社区参考来源

| 来源 | 借鉴点 |
|------|--------|
| [quant67 P2C/EWMA](https://quant67.com/post/algorithms/69-load-balancing/load-balancing.html) | score 公式、EWMA 半衰期、冷启动惩罚、熔断三态、panic threshold |
| [relay-pulse](https://github.com/prehisle/relay-pulse) | 真实 token 探针、degraded_weight、stagger_probes、指数退避 |
| [LiteLLM Router](https://docs.litellm.ai/docs/routing) | cooldown 机制、分类 fallback（context_window / content_policy）、latency-based-routing |
| [OpenRouter 双层路由](https://openrouter.ai/blog/insights/model-routing) | 30s 故障窗口、价格逆平方加权、provider 排序策略 |
| [NewAPI #6095](https://github.com/QuantumNous/new-api/issues/6095) | retry 下标竞态——降低 RetryTimes 的依据 |
| [claude-code-hub #916](https://github.com/ding113/claude-code-hub/issues/916) | 流末尾 message_stop 校验、controller.error() vs close() |
| [kiro_guard tee 模式](docs/patches/kiro-guard-tee-mode-2026-07-27.md) | 已在生产的截断续写方案，不重叠 |
