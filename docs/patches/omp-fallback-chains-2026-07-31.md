# OMP `config.yml` fallbackChains 重构（2026-07-31）

把 OMP 的 fallback 链从纯 deepseek 线扩展为 router 互备 + claude 线 + vision/tiny 覆盖。

## 1. 根因

故障发生时：`anyrouter/claude-opus-4-8:xhigh` 请求失败（anyrouter.top 上游 503），OMP 的 `modelFallback: true` 将其降级到 `@default`（当时为 `zg-newapi/deepseek-official-v4-flash`），该模型不支持图像输入，导致 vision 请求连带失败。agentrouter（:8788）4 key 全健康，但当时不在任何 fallback 链上。

## 2. 改动

`~/.omp/agent/config.yml` 的 `retry.fallbackChains` 节：

| 链 | 改动 | 原因 |
|---|---|---|
| `default` | 重写：`agentrouter/claude-opus-5` → `agentrouter/claude-opus-4-8` → `zg-newapi-anthropic/claude-opus-5` → `zg-newapi/deepseek-official-v4-flash` → `zg-newapi/deepseek-v4-flash` | 原链全是 deepseek，与改动当时的默认 Claude 路由语义不匹配；当前 `modelRoles.default` 后续已由用户另行调整 |
| `slow` | 尾部加 `agentrouter/claude-opus-5` + `agentrouter/claude-opus-4-8`；`zg-newapi/claude-opus-5` 改为 `zg-newapi-anthropic/claude-opus-5`（修复悬空引用） | deepseek/gpt 失败后落到 agentrouter |
| `vision` | 新加：`agentrouter/claude-opus-5` → `agentrouter/claude-opus-4-8` → `zg-newapi-anthropic/claude-opus-5` | agentrouter 断线时仍有 NewAPI claude 支持图像 |
| `tiny` | 新加：`zg-newapi/cline-free/glm-5.2` → `codebuddy/glm-5.2` → `zg-newapi/deepseek-v4-flash` | cline free 日额打满后降级 |
| `modelRoles.vision` | 新加 `agentrouter/claude-opus-5:high` | 让 `inspect_image` 选择 vision-capable 模型；但该工具直接调用模型，不经过主会话 `TurnRecovery`，自身没有 retry/fallback，也不消费 `fallbackChains.vision`。该链只保护主会话处于 `@vision` 角色时的 assistant 轮次 |
| `anyrouter/*` | 新加通配链：`anyrouter/*` → `agentrouter/*` → `zg-newapi-anthropic/*` | 按模型键前缀匹配，优先于 role/default 链；手动选择 anyrouter 模型也可触发 |
| `agentrouter/*` | 新加通配链：`agentrouter/*` → `zg-newapi-anthropic/*` | agentrouter 模型失败后落到 NewAPI claude 线 |

## 3. 验证

- YAML 格式校验通过；全部 19 条模型引用逐条确认在 `models.yml` 中存在（通配链 `*` 不在此列）。
- OMP 有效配置读取确认 `modelRoles.vision = agentrouter/claude-opus-5:high`，并解析出 `anyrouter/*` → `agentrouter/*` → `zg-newapi-anthropic/*` 与 `agentrouter/*` → `zg-newapi-anthropic/*`。
- 使用临时 `--config` overlay（仅该进程：`modelFallback=true`、`maxRetries=1`、`baseDelayMs=100`、`maxDelayMs=5000`）分别请求 `anyrouter/claude-opus-5:high`（90 秒上限）和 `anyrouter/claude-opus-4-8:high`（115 秒上限）；两次都一直停留在 anyrouter，最终 `Deadline exceeded`，没有 fallback warning/event，也没有 agentrouter 成功响应。
- 8789 模型请求（不是 health）实测 16.87 秒返回代理层 HTTP 502，detail 为上游所有 key HTTP 503；相同 overlay 下定向 `agentrouter/claude-opus-5:high` 请求 9.50 秒完成，模型响应本身约 5.10 秒并准确返回 `AGENTROUTER-DIRECT-OK`。这证明首跳确实故障且目标 provider 可用，但由于同一 OMP anyrouter 命令没有最终成功，不能据此判定 wildcard E2E 已执行。
- 因隔离测试未证明 wildcard 降级，按安全门禁未向持久 `retry` 写入 `maxRetries: 3`；有效配置仍为默认 `maxRetries=10`、`baseDelayMs=500`、`maxDelayMs=300000`。因此也没有运行依赖该持久值的 120 秒冒烟。

## 4. 遗留

- anyrouter 上游仍 503（公益站间歇）；更重要的是，降低重试次数的隔离 OMP 请求仍在 anyrouter 等到全局 deadline，没有观察到 provider 切换。端到端自动降级（anyrouter→agentrouter→zg-newapi-anthropic）仍未证明，因此没有持久降低全局重试次数。后续需先查明单次请求为何未把 502/503 交给 `TurnRecovery`，再重复同一 overlay 测试；不得用 health=200 代替模型成功。恢复后还需实测其 1M 上下文是否真实（`models.yml` 标注 1M，agentrouter 同模型标 200K）。
- `defaultThinkingLevel: auto` 在 agentrouter 上偶发触发 content-blocked（已有 400 日志），如频繁出现可降级到 `high` 或 `medium`。

> 安全：本文档不含任何 API key 或凭据。