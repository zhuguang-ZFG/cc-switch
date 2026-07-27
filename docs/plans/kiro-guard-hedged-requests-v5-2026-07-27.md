# kiro_guard Hedged Requests（并行竞速）立项 — v5

日期：2026-07-27 · 状态：立项（未动工）· 前置：tee 模式已全功能在线

## 背景与收益

现网「竞速」是权重级：一次请求只打一个渠道，失败才重试下一个。单次请求的
运气（上游排队）无法被路由层治疗——实测大 prompt FRT 长尾 20-44s，而 p50
已在改善。社区标准解法：**hedged requests**（Envoy request hedging、Google
《The Tail at Scale》、bytesizego hedge 库）——首字节超过动态阈值未返回，
向第二渠道发备份请求，先到先得，输的取消。

预期收益：交互请求 FRT p95/p99 从 20-44s 压到 ~p50 双通道最小值（估计
8-15s），「卡顿感」主要来源消除。

## 设计

落点：**kiro_guard**（8 实例已 tee 全部流量，有 SSE 缓冲/取消/合并全套管道）。

- **触发**：请求发出后 `hedge_delay`（动态 = 该渠道近期 TTFT 的 p95，初值 8s，
  上下限 4s/20s）未收到首字节 → 向「同 tier 权重第二高且健康」的渠道发备份
- **决胜**：先到首字节者继续 tee 给客户端；输家连接取消（context cancel），
  其已消耗的 upstream 配额记日志（hedge_waste 指标）
- **硬门槛**（保护公益池，防双倍打爆并发上限）：
  - 仅当 tier 内 ≥2 个探针健康渠道才允许 hedge
  - hedge 率上限：每实例每分钟最多 N 次（初值 6），超出退化为单通道
  - 公益池渠道（100xlabs 系）被 hedge 命中计数，optimizer 可将高 hedge 率
    渠道降权（正反馈：慢→被 hedge→降权→更少流量→恢复）
  - 非流式请求不 hedge（分类器等短请求不值得）
- **与 tee 续写的关系**：hedge 发生在首字节前；tee 续写处理首字节后的
  截断。两者正交，共用「取消/切换上游」原语

## 同批纳入的 tee 积压项（guard 大改一波做完）

- **P2-11 线程/连接上限**：实例级并发闸门（semaphore），防 hedge 放大突发
- **P2-13 慢路径错误呈现**：tee 续写/hard 失败时给客户端的 SSE 错误事件
  带可诊断信息（req_id + reason），不再裸断
- **P2-14 cyrillic bypass 收敛**：现 AR-only 的 Cyrillic-Bypass 逻辑随 guard
  重构整理（保持 AR 限定，勿扩散）
- **P2-12 残余**：SOFT_RETRY≥2 的多轮累积式 continuation（hedge 管道复用
  后顺手做）

## 验收标准

1. 压测客户端在 FRT p95 处人为注入 30s 慢上游：客户端首字节 ≤ 阈值+1s
2. hedge_waste（被取消请求的上游 token 消耗）< 总消耗 5%
3. 100xlabs 并发限制事件不显著增加（对照前一日同时段）
4. 现网灰度：先只在 8404/8405（100xlabs 实例）开 `KIRO_GUARD_HEDGE=1`，
   观察 24h 无回归再全量

## 风险

- 公益池 TOS/并发：hedge 是双倍请求，必须先有门槛再灰度；失控开关
  `KIRO_GUARD_HEDGE=0` 一键回退
- 取消传播：输家请求的 upstream cancel 必须可靠（Go context / Python
  close），否则 hedging 变成纯浪费
- k3/grok 映射阀被 hedge 时权重体系不变——hedge 选择读实时 weight，
  与 optimizer 无耦合

## 参考

- Envoy request hedging 文档；bytesizego「Cut your p99 latency in Go with
  hedged requests」（2026-03）；Google《The Tail at Scale》
- 错误分级冷却（本批已先做的 #2）：route_optimizer v5 channel_error_classes
