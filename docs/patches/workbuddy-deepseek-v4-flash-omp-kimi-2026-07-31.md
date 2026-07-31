# WorkBuddy DeepSeek V4 Flash 本地代理接入（OMP + Kimi）（2026-07-31）

将 WorkBuddy Desktop（CodeBuddy 后端）的 `deepseek-v4-flash` 经本机 `codebuddy2openai` 转换器暴露给 OMP 与 Kimi Code CLI。

## 1. 结论先行

无需改转换器。`codebuddy2openai` 的 `DEFAULT_MODELS` 本就含 `deepseek-v4-flash`（CodeBuddy 官方后端 `copilot.tencent.com` 内置模型，与 glm-5.2 同路径），转换器直接透传即可；本次只给两个客户端补模型别名。

## 2. 调用链

```text
OMP / Kimi Code CLI
        |
        v
http://127.0.0.1:8787/v1  (codebuddy2openai, 直连 CodeBuddy 后端)
        |
        v
WorkBuddy Desktop 已登录会话 (copilot.tencent.com)
```

## 3. 客户端接入

- OMP `~/.omp/agent/models.yml` 的 `codebuddy` provider 增加 `deepseek-v4-flash`（1M 上下文 / 32K 输出；按 models.yml 既有 deepseek 惯例——不返回 `reasoning_content`——不设 `reasoning: true`）。
- Kimi `~/.kimi-code/config.toml` 增加 `[models."codebuddy/deepseek-v4-flash"]`，经既有 `[providers.codebuddy]`（`127.0.0.1:8787`）。

## 4. 验证

```text
POST /v1/chat/completions (deepseek-v4-flash, 非流式)  -> WORKBUDDY-DSV4F-OK
max_tokens 32768 / 65536 探测                       -> 均 200（后端输出上限 ≥64K，客户端按 32K 保守配置）
流式                                            -> SSE 7 chunk + [DONE]，OK
omp models codebuddy                             -> deepseek-v4-flash 1M context / 33K max-out
kimi doctor config <config.toml>                 -> OK
omp -p --model codebuddy/deepseek-v4-flash       -> OMP-DSV4F-OK
kimi -m codebuddy/deepseek-v4-flash -p           -> KIMI-DSV4F-OK
glm-5.2 / gpt-5.6-sol 未改动                      -> 回归不受影响
```

## 5. 与其它 deepseek-v4-flash 来源的区别

| 来源 | 完整别名 | 走法 |
|---|---|---|
| WorkBuddy/CodeBuddy 后端 | `codebuddy/deepseek-v4-flash` | 本机 8787 转换器 |
| NewAPI 官方直连 ch42 | `zg-newapi/deepseek-official-v4-flash` | 远程 NewAPI |
| NewAPI 聚合池（ch15/40/126） | `zg-newapi/deepseek-v4-flash` | 远程 NewAPI |
| Cline 账号池 | `zg-newapi/cline-deepseek-v4-flash` | VPS cline-proxy |

四个来源互相独立；按完整别名选择，勿混淆。`deepseek-v4-pro` 在转换器 `DEFAULT_MODELS` 中同样可用，本次未接入。

## 6. 运行前提与排障

与 glm-5.2 相同：WorkBuddy Desktop 保持已登录、`codebuddy2openai` 转换器监听 `127.0.0.1:8787`（`~/.kimi-code/proxies/codebuddy2openai/watchdog.ps1` 守护）。`/v1/models` 返回 `401` 时检查调用是否带转换器 Bearer key，不代表 WorkBuddy 模型不可用。
