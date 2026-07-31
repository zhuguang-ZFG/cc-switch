# anyrouter.top 接入调查：1M 上下文强制 + 上游 503（2026-07-31）

调查 anyrouter.top（公益 Claude 中转，Claude Code 池）为何经本机代理调用全部失败，以及是否可接入 NewAPI 聚合池。结论：**上游协议门槛 + 当前无容量，暂不接入**。

## 1. 结论

- anyrouter 上游**协议正常可达**（`/v1/models` 返回完整模型列表），但不是配置问题导致失败，而是**协议用法 + 上游容量**两个独立门槛。
- **暂不接入 NewAPI**：Claude 模型需 Anthropic Messages 格式（NewAPI 渠道是 OpenAI 格式，需协议转换层）；且带上正确头部后上游返回 503（Claude 池无容量）。

## 2. 关键发现

### 认证方式（代理现有实现有误）

| Header | 结果 |
|---|---|
| `x-api-key: sk-...`（代理当前用法） | 各种 400/503 混合 |
| `Authorization: Bearer sk-...` | 正确进入校验，返回「1m 上下文」提示 |

代理 `anyrouter-proxy.py` 用 `x-api-key` 传上游 key，应改为 `Authorization: Bearer`。

### 1M 上下文强制

- **不带** `anthropic-beta: context-1m-2025-08-07` → `{"error":"1m 上下文已经全量可用，请启用 1m 上下文后重试"}`
- **带** `anthropic-beta: context-1m-2025-08-07` → `{"error":{"message":"Service Unavailable","type":"error"}}`
- anyrouter 网站后台**无 1M 开关**；Linux.do 社区方案（模型名加 `[1m]` 后缀）实测无效——那是 Claude Code 客户端本地语法，非上游协议字段。
- 试过所有 beta 组合（`claude-code-20250219`、`prompt-caching-2025-02-19`、`output-128k-2025-02-19` 等）+ 完整 Claude Code 指纹头，结果一致：503。

### 格式限制

- `GET /v1/models`：OpenAI 格式可用，列出 17 个模型（claude 全家 + gpt-5-codex + gpt-5.6-sol + gemini-2.5-pro）。
- `POST /v1/chat/completions`（OpenAI）：所有模型 `当前 API 不支持所选模型`。
- `POST /v1/messages`（Anthropic）：gpt-5.6-sol / gpt-5-codex 同样 `当前 API 不支持所选模型`。
- **结论：anyrouter 只服务 Anthropic 格式的 Claude 模型**，GPT 模型列在模型列表但不可调用。

## 3. 若要接入需做的事

1. 修代理：`x-api-key` → `Authorization: Bearer`，且按需注入 `anthropic-beta: context-1m-2025-08-07`。
2. 建协议转换层（Anthropic Messages ↔ OpenAI Chat Completions）——NewAPI 渠道只吃 OpenAI 格式。
3. 上游 503 解除后才有意义——当前 Claude 池无容量，属于上游状态，非可修配置。

## 4. 现状（2026-07-31）

- 本机 `anyrouter-proxy` 已按 `--host 0.0.0.0` 启动（Tailscale 可达），但上游 503 时无法冒烟。
- NewAPI 聚合池不受影响：Claude 走 ch18/26/27/28/45（linxi-k40 / gorouter / agentrouter），gpt-5.6-sol 走 ch16/25/30/34/44/45。
- 已记录：`anyrouter` provider 在 OMP/Kimi 配置中保持现状，OMP fallback 链已有 `anyrouter/*` → `agentrouter/*` → `zg-newapi-anthropic/*` 兜底。

> 安全：本文档不含 anyrouter session cookie、API key、NewAPI token。
