# WorkBuddy sol 上下文虚高修复 + agentrouter 不稳定诊断（2026-07-31）

排查 agent 模型不稳定，定位到两个不同根因：WorkBuddy gpt-5.6-sol 是**上下文配置虚高**（已修复），agentrouter claude-opus-5 是**上游间歇限流**（固有，key 池缓解）。

## 1. WorkBuddy gpt-5.6-sol：上下文配置虚高（已修复）

**现象**：agent 会话涨到 526 消息（带 12 个工具）即报 `400 "Your input exceeds the context window"`，反复失败。

**根因**：配置 `max_context_size=1050000`（1M，按官方 gpt-5.6-sol 规格误配），但 freemodel.dev 第三方中转的 gpt-5.6-sol 实测可靠工作区仅 ~343K：

```text
300K 输入 -> 285K prompt，11s 成功
343K 输入 -> 343K prompt，146s 成功（慢）
526 消息 agent 会话 -> 400 context-window-exceeded（超实际上限）
```

agent 按 1M 不压缩会话（compact 触发点 ~950K），涨到 >343K 就撞上游真上限报错。

**修复**：Kimi `~/.kimi-code/config.toml` 与 OMP `~/.omp/agent/models.yml` 中 `codebuddy/gpt-5.6-sol` 的 `max_context_size`/`contextWindow` 从 `1050000` 降为 `262144`（256K）。agent 在 ~206K 即压缩会话，既避开超限报错，又避开 >300K 的 146s 慢速区（256K 内约 11s）。已校验生效（OMP 显示 262K，Kimi max_context=262144）。

## 2. agentrouter claude-opus-5：上游间歇限流（固有，key 池缓解）

**现象**：间歇性失败/高延迟。

**根因**：agentrouter 上游激进的间歇性限流（429，报文"100M tokens/60s"为误导，实为 burst 限制）。表现：
- 限流期 4 key 轮换也全打满 → 请求等待冷却（高延迟）或偶发全 key 耗尽（失败）。
- 偶发 `content-blocked`（上游内容审核，如 opus-4-8 的 1045 消息请求）。
- 偶发 `stream finish=None` 的截断流。

这是 agentrouter 上游行为，本地代理（4 key 池 + 冷却 + 等待重试）已尽力缓解但无法根除。**当前实测 6/6 成功、0 key 冷却**——间歇性，限流期过了即稳定。要更稳需加更多 key 分摊。

## 3. 对照：WorkBuddy glm-5.2 不虚高（无需改）

glm-5.2 也配了 1M，但走 **CodeBuddy 官方后端**（copilot.tencent.com，真 1M 模型），实测 300K/500K/800K 输入均正常（654K prompt，19-25s）。与 freemodel 中转的 sol（~343K 窗口）不同源，故 glm-5.2 的 1M 配置正确，无需降。

## 4. 修复清单

| 项 | 改动 | 文件 |
|---|---|---|
| Kimi gpt-5.6-sol | max_context_size 1050000 → 262144 | `~/.kimi-code/config.toml` |
| OMP gpt-5.6-sol | contextWindow 1050000 → 262144 | `~/.omp/agent/models.yml` |

> 安全：本文档不含任何 API key、凭据。
