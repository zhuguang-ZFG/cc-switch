# OMP 角色推荐纪律与 advisor sota 锁定（2026-08-20）

## 事件

当日评审 OMP 角色分配时，AI 建议把 `advisor` 从 `zg-newapi/omp-sota-claude-opus-5:high`
（sotamodel 免费渠道）切到 `zg-newapi-anthropic/claude-opus-5:high`（TTFT 网关 →
justwoker 付费渠道），理由是 sotamodel 免费日额度每天耗尽导致 advisor 停机。
用户批准后上线，约 20 分钟内被观察到"一直在耗 token"：advisor 是高频后台角色
（活跃会话 ~20s/轮，prompt 26k+ tokens/轮），付费路径下每 3 分钟烧 ¥0.2+
justwoker 额度。用户叫停并明确约束：**advisor 只能用 sota 模型**。

## 常驻约束

- `modelRoles.advisor` 固定为 `zg-newapi/omp-sota-claude-opus-5:high`，
  config.yml 有注释，`scripts/ops/test_omp_routes.py`
  `test_advisor_role_is_pinned_to_sota` 有硬门禁；
- sotamodel 日额度耗尽 → advisor 停机，是**可接受取舍**，不得以此为由改指付费路由；
- advisor 停机后恢复方式：OMP 会话内 `/advisor` 或 reload config。

## 推荐纪律（向 AI 助手）

给用户做角色/链路推荐前，必须先算账再开口：

1. **调用频率**：这个角色多久触发一次？（advisor ~20s/轮 ≫ default 用户轮次）
2. **计费路径**：目标渠道是免费还是付费？单次成本 × 频率 = 日成本多少？
3. **用户意图**：用户为该角色选免费模型，通常就是刻意的成本决策，
   "稳定性更好"不构成切付费的理由；
4. 推荐里必须写明成本影响，不允许只说优点。

反例即本次：只看到"advisor 每天停机"的缺点就推荐付费路径，
没算高频调用 × 付费单价，也没确认用户选择免费模型的意图。
