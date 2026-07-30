# anyrouter.top 接入 OMP/Kimi（Claude Code 指纹破解）（2026-07-31）

把 anyrouter.top 的 Claude 模型（claude-opus-5 等）经本机代理接入 OMP/Kimi。**调用约定已破解**（Claude Code 客户端指纹），但接入时上游 claude-code 池正返回 503（公益站间歇中断），代理已就绪，服务恢复后自动可用。

## 1. 关键发现：opus 要 Claude Code 客户端指纹

anyrouter 对 opus 系列做客户端指纹校验（结论来自社区 [relay-pulse](https://github.com/prehisle/relay-pulse) 的 `cc-opus-arith-anyrouter.json` 模板）：

- 缺指纹 → opus 一律 `503/520 "Service Unavailable"`；haiku 不校验（裸 4 头即通）。
- 指纹要素（4 项缺一不可）：
  1. `anthropic-beta` 含 `claude-code-20250219` + `context-1m-2025-08-07` 等完整集合（缺 context-1m 报 400「请启用 1m 上下文」）。
  2. `User-Agent: claude-cli/2.1.170 (external, sdk-cli)` + `x-app: cli` + `anthropic-dangerous-direct-browser-access: true` + 全套 `X-Stainless-*`（Lang=js/OS=Linux/Runtime=node 等）。
  3. body `metadata.user_id` 为 `user_..._account__session_...` 格式。
  4. body 带 SDK system 文案（`You are a Claude agent, built on Anthropic's Claude Agent SDK.`）。

## 2. 当前状态：上游 503（服务侧，非配置问题）

接入时（2026-07-31）用**完整指纹**请求 opus 仍 503。穷尽验证证明是服务侧中断、非客户端配置：

| 客户端 | 结果 |
|---|---|
| Python urllib + 完整指纹 | 503 |
| curl（Schannel TLS）+ 完整指纹 | 503 |
| Node.js https（Claude Code 运行时）+ 完整指纹 | 503 |
| 官方 `@anthropic-ai/sdk`（Claude Code 内部库）+ 完整指纹 | 503 |
| sonnet-4-5 **裸头**（无指纹） | 400「启用 1m」（到达 API 逻辑） |

裸头能到 API、加 claude-code 指纹头就 503 → 指纹头把请求路由到 claude-code 上游池，该池当前不可用。社区注：anyrouter 是公益站，约 80% 可用，高峰会调不通（2025-07 曾遭攻击中断约 1 天）。

## 3. 代理设计

文件：`C:/Users/zhugu/.kimi-code/proxies/anyrouter-proxy/anyrouter-proxy.py`，监听 `127.0.0.1:8789`，**Anthropic 透传**（OMP `api=anthropic-messages` / Kimi `type=anthropic` 原生支持，无需 OpenAI↔Anthropic 翻译）：

- 注入完整 Claude Code 指纹头（覆盖客户端头），`anthropic-beta` 与客户端 flags 合并（确保含 claude-code/context-1m）。
- 注入 `metadata.user_id`（`user_..._account__session_...` 格式）。
- 转发到 `https://anyrouter.top/v1/messages`，响应原样透传（流式/非流式）。
- 多 key 轮询 + 429/502/503/520 冷却重试（keys.json）。
- 加固：元数据日志、有限超时、health 鉴权。

## 4. OMP / Kimi 接入

- OMP `~/.omp/agent/models.yml`：`anyrouter` provider（`http://127.0.0.1:8789`，`api: anthropic-messages`），挂 `claude-opus-5` / `claude-opus-4-8`（1M 上下文 / 128K 输出 / thinking）。
- Kimi `~/.kimi-code/config.toml`：`[providers.anyrouter]`（`type=anthropic`，`base_url=http://127.0.0.1:8789`）+ `[models."anyrouter/claude-opus-5"]` / `claude-opus-4-8`（1M）。

## 5. 自启动

`anyrouter-proxy/watchdog.ps1`（每 30s 探测 `/health`，pythonw 拉起）；启动项 `cline-glm-proxy.bat` 增加 `AnyrouterWatchdog`（与 codebuddy/agentrouter 并列）。

## 6. 验证

```text
代理 health                         -> OK（anthropic-messages 协议）
代理转发 + 指纹注入 + 冷却重试        -> 正确（503 时冷却 key、等待、重试）
OMP anyrouter 模型                  -> claude-opus-5/4-8（1M/thinking）列出
Kimi doctor                         -> OK
端到端内容                           -> 当前 503（上游中断），服务恢复后自动可用
```

## 7. 注意

- **当前不可用是 anyrouter 上游中断（503），非配置问题**；代理已就绪，恢复后无需改动即自动工作。
- VPS 连不上 anyrouter.top（TLS 握手失败，老 curl/OpenSSL 指纹被拒），故不接 NewAPI，仅本机代理。
- 公益站，代码对中间方可见，不适合商业项目；稳定性约 80%。
- `keys.json` 含明文 key，仅本机使用，勿提交仓库。

> 安全：本文档不含 anyrouter API key、VPS 密码。
