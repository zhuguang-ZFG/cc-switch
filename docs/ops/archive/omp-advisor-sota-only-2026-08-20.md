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

## 续：sota 停机期间 Zen 免费模型顶上（当日用户决策）

约束不变（advisor 主选永远锁 sota），但为消除每日额度耗尽后的停机窗口，
新增兜底链（config.yml fallbackChains）：

```
zg-newapi/omp-sota-claude-opus-5:
  - zg-newapi/muse-spark-1.2-contributor-free
  - zg-newapi/hy3-free
```

- 链上全部是 Zen 免费模型（ModelRatio=0），零成本，符合"advisor 不烧钱"的约束本意；
- 质量降级（免费小模型给建议）但 advisor 不停机；
- 门禁兼容性：`validate_sota_upgrade_only` 只禁止 sota 别名作为链候选，
  以 sota 别名为链键指向免费模型是合法方向；test_omp_routes 38 项全绿；
- **待观察项（已实测，2026-08-27）**：advisor **不消费** fallbackChains。
  证据：ch93 当日手动禁用（balance 0，`status_reason=manual operation`）期间，
  `omp.2026-08-27.*.log` 从 12:44 到 18:36 持续 `advisor turn failed:
  503 No available channel for model omp-sota-claude-opus-5 under group default
  (param=model_not_found)`，约每 2 分钟一次，从未切到链上的 muse-free；
  同期 `omp-sota-escalation.js` 多次 `Extension handler timed out (30s)`。
  结论：上述兜底链对 advisor 是死配置（对主会话其他角色仍有效）。
  **2026-08-27 用户决策：维持停机可接受取舍**——不加垫底渠道，噪声日志容忍，
  ch93 上游充值后自然恢复。若日后重议，候选方向为 NewAPI 层 p0 免费垫底渠道
  （公开名 omp-sota-claude-opus-5 映射免费上游，零成本、selector 不变、不碰门禁）。
  见 `docs/ops/ox-alpha-removal-2026-08-27.md` 同日日志审查节。

**2026-08-28 落地：死链移除（用户同意）**——`retry.fallbackChains` 中的
`zg-newapi/omp-sota-claude-opus-5` 兜底链已从活配置删除（12 → 11 键，
YAML 校验通过，diff 仅 3 行；备份
`config.yml.bak-20260828-*-sota-dead-chain-removal`）。复核依据：当前配置中
sota selector 的唯一消费方是 `modelRoles.advisor`，且无任何角色链以 sota
为候选，故 L54 括注"对主会话其他角色仍有效"在现配置下已无实际触发路径；
若未来 advisor 之外的角色/手动切换启用 sota，故障将硬失败而非落到免费
模型——与"advisor 不消费免费垫底"的既有取舍一致。ch93 充值恢复路径与
2026-08-27 决策均不受影响。
