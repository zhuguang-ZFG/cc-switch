# OMP 推理角色链移除 DeepSeek（2026-08-28）

**Status:** Active（plan/designer/slow 三个推理角色的 fallback 可达集不含任何
DeepSeek；门禁 `test_reasoning_role_chains_exclude_deepseek` 强制，40/40 绿）
**Scope:** `~/.omp/agent/config.yml`（`retry.fallbackChains`）、
`scripts/ops/test_omp_routes.py`（路由门禁）、`.trellis` spec（本地）

## 背景：k3 → DeepSeek 路由调查（2026-08-27）

用户发现 k3（plan/designer 主选）会话曾落到 DeepSeek。日志取证
（`omp.2026-08-27.6440.log`，pid 6440 会话）：

- 16:21:34 k3 turn → `400 Invalid request Error`（kimi 侧 generic
  `invalid_request_error`，无详情）；
- 16:24:14 同会话 `agent_end` 落在 `deepseek-v4-flash`；
- 16:35:41 k3 恢复正常（冷却到期回切）。

原始 400 请求体
（`~/.omp/logs/http-400-requests/1787818894839-*.json`）：`model:k3`、
345 条消息、`thinking:effort=xhigh`；同形状请求 16:35 返回 OK →
**瞬时上游故障**。NewAPI 侧 k3 流量 24h 内 100% 归 ch33，排除网关路由
错误。结论：**OMP 角色链 fallback 按设计工作**（当时候选梯
`plan: [claude-opus-4-8, deepseek-v4-flash, intern-s2-preview]`、
`designer: [claude-opus-5, deepseek-v4-flash]`，`retry.modelFallback: true`、
`fallbackRevertPolicy: cooldown-expiry`），但暴露了一个政策问题：高价值
推理角色瞬时故障后降级到快速廉价杂活模型，是能力悬崖，且模型切换掩盖
400 真因。

## 用户决策（2026-08-28）

**DeepSeek 只干杂活；plan/designer 链不能有 DeepSeek。** 随后用户确认
`slow` 同属推理角色，一并贯彻。最终链：

| 角色 | 移除后 | 移除前尾部 |
|---|---|---|
| plan | `[claude-opus-4-8, intern-s2-preview]` | deepseek-v4-flash |
| designer | `[claude-opus-5]` | deepseek-v4-flash |
| slow | `[claude-opus-4-8, k3]` | deepseek-v4-flash |

行为：耗尽推理跳后**硬失败**，`cooldown-expiry` 自动回切主选。DeepSeek 在
活配置中的全部剩余位置（均杂活档，合规）：`smol` 角色链头、
`muse-spark-1.2-contributor-free` 模型链头。

## 落地与门禁演进

- 配置备份（live）：`config.yml.bak-…-plan-designer-nodeepseek`、
  `config.yml.bak-…-slow-nodeepseek`；omp 本地仓提交 `7f4b1f2`（plan/
  designer）、`7c2190f`（slow）。
- 门禁（cc-switch 仓，`test_omp_routes.py`）三步演进：
  1. `d407bc39`：新增 `test_plan_designer_chains_exclude_deepseek`
     （39→40 tests）；
  2. `5fbfe0e9`：扩至 slow，改名
     `test_reasoning_role_chains_exclude_deepseek`；
  3. `30fdc95a`：补**传递闭包**——角色主选（`modelRoles`）或链内任意一跳若
     以 `provider/model` 键挂了模型级链，同样不得可达 DeepSeek（新增
     `_chain_reach` 助手；阴性自测：注入
     `zg-newapi/k3: [deepseek-v4-flash]` 即被 plan 角色拦截）。
- 政策条文落 `.trellis/spec/ops/zg-gateway-claude-code.md`（本地，
  gitignored）：Contracts 不变式 bullet（紧邻 default 禁跨模型不变式）+
  验证矩阵行，直接形与传递形均由门禁强制。

## 既知接受项（不改动）

- **400 会触发模型级 fallback**：OMP 核心 `modelFallback: true` 单标志，
  不按错误类别过滤（400/429/5xx 同待遇）——接受。链改后即使 400 误触发
  fallback，也在推理跳内耗尽后硬失败，不再落到杂活模型。
- **k3 池是 active/standby 而非负载均衡**：ch33 p50 / ch108 p49 /
  ch110 p6 三级优先 → 100% 归 ch33；维持（与既有 Sol 池模式一致）。
- sensenova-6.7-flash-lite 429（单点探测，"Server is busy"）：单次探测
  不成趋势，按生产规则不动。

## 关联变更（同日，另档）

- advisor sota 死链移除：`docs/ops/omp-advisor-sota-only-2026-08-20.md`
  追加 2026-08-28 落地节（omp 仓 `2265f01`，cc-switch `2735f07e`）。
- 压缩候选梯 r6（删 zai 死条目、glm-5.2 复测维持保留态）：
  `docs/ops/omp-global-compaction-model.md` 追加 2026-08-28 节（omp 仓
  `4a6f429`，cc-switch `cbc71097`）。

## 文档同步

- 本 runbook。
- 现态文档无需改：无当前状态文档描述 plan/designer/slow 链内容；日期
  runbook（`agentrouter-opus-retry-fix-2026-08-15.md` 等）为历史，不重写。
- spec 更新仅本地（`.trellis/` gitignored）。
