# OMP `config.yml` fallbackChains 重构（2026-07-31）

把 OMP 的 fallback 链从纯 deepseek 线扩展为 router 互备 + claude 线 + vision/tiny 覆盖。

## 1. 根因

`anyrouter/claude-opus-4-8:xhigh` 请求失败时（anyrouter.top 上游 503），OMP 的 `modelFallback: true` 把它降级到 `@default`（`zg-newapi/deepseek-official-v4-flash`），该模型不支持图像输入，导致 vision 请求连带失败。而 agentrouter（:8788）4 key 全健康，只是不在任何 fallback 链上。

## 2. 改动

`~/.omp/agent/config.yml` 的 `retry.fallbackChains` 节：

| 链 | 改动 | 原因 |
|---|---|---|
| `default` | 重写：`agentrouter/claude-opus-5` → `agentrouter/claude-opus-4-8` → `zg-newapi-anthropic/claude-opus-5` → `zg-newapi/deepseek-official-v4-flash` → `zg-newapi/deepseek-v4-flash` | 原链全是 deepseek，与当前默认模型（`agentrouter/claude-opus-5:xhigh`）语义不匹配 |
| `slow` | 尾部加 `agentrouter/claude-opus-5` + `agentrouter/claude-opus-4-8`；`zg-newapi/claude-opus-5` 改为 `zg-newapi-anthropic/claude-opus-5`（修复悬空引用） | deepseek/gpt 失败后落到 agentrouter |
| `vision` | 新加：`agentrouter/claude-opus-5` → `agentrouter/claude-opus-4-8` → `zg-newapi-anthropic/claude-opus-5` | agentrouter 断线时仍有 NewAPI claude 支持图像 |
| `tiny` | 新加：`zg-newapi/cline-free/glm-5.2` → `codebuddy/glm-5.2` → `zg-newapi/deepseek-v4-flash` | cline free 日额打满后降级 |

## 3. 验证

- YAML 格式校验通过。
- 全部 19 条模型引用逐条确认在 `models.yml` 中存在。
- agentrouter 直测 `claude-opus-4-8` / `claude-opus-5` 均返回 `OK`。

## 4. 遗留

- anyrouter 上游仍 503（公益站间歇），恢复后需实测其 1M 上下文是否真实（models.yml 标注 1M，agentrouter 同模型标 200K）。
- `defaultThinkingLevel: auto` 在 agentrouter 上偶发触发 content-blocked（已有 400 日志），如频繁出现可降级到 `high` 或 `medium`。

> 安全：本文档不含任何 API key 或凭据。